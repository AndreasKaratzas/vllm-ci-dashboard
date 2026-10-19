<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import type { Tone } from '@/domain/format'
import { cn } from '@/lib/utils'

const props = defineProps<{ tone: Tone }>()

const TONE_CLASSES: Record<Tone, string> = {
  pass: 'border-success/40 bg-success/15 text-success',
  hard: 'border-destructive/40 bg-destructive/15 text-destructive',
  soft: 'border-warning/40 bg-warning/15 text-warning',
  info: 'border-info/40 bg-info/15 text-info',
  neutral: 'border-border bg-muted text-muted-foreground',
  'not-targeted': 'border-border bg-transparent text-muted-foreground',
}
const classes = computed(() => cn('font-medium', TONE_CLASSES[props.tone]))
</script>

<template>
  <Badge variant="outline" :class="classes"><slot /></Badge>
</template>
