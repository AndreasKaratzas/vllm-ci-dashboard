<script setup lang="ts">
import { computed } from 'vue'
import { Card, CardContent } from '@/components/ui/card'
import { integer, type Tone } from '@/domain/format'

const props = withDefaults(defineProps<{
  eyebrow: string
  title: string
  current: number
  total: number
  available?: boolean
  meta?: string
  tone: Tone
  actionLabel?: string
}>(), { available: true })
defineEmits<{ open: [] }>()

const available = computed(() => props.available !== false && props.total > 0)
const rate = computed(() => (available.value ? Math.min(100, (Math.max(0, props.current) / props.total) * 100) : 0))
const RADIUS = 42
const circumference = 2 * Math.PI * RADIUS
const STROKE: Record<Tone, string> = {
  'is-success': 'stroke-success',
  'is-danger': 'stroke-destructive',
  'is-warning': 'stroke-warning',
  'is-info': 'stroke-info',
  'is-neutral': 'stroke-muted-foreground',
  'is-not-targeted': 'stroke-muted-foreground',
}
</script>

<template>
  <Card class="@container/card">
    <CardContent class="flex items-center gap-6">
      <div class="relative size-28 shrink-0">
        <svg viewBox="0 0 100 100" class="size-full -rotate-90" aria-hidden="true">
          <circle cx="50" cy="50" :r="RADIUS" class="fill-none stroke-muted" stroke-width="10" />
          <circle
            cx="50"
            cy="50"
            :r="RADIUS"
            class="fill-none transition-[stroke-dashoffset] duration-700"
            :class="STROKE[tone]"
            stroke-width="10"
            stroke-linecap="round"
            :stroke-dasharray="circumference"
            :stroke-dashoffset="circumference * (1 - rate / 100)"
          />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <strong class="text-xl tabular-nums">{{ available ? `${rate.toFixed(1)}%` : '—' }}</strong>
          <span class="text-xs text-muted-foreground tabular-nums">{{ available ? `${integer(current)} / ${integer(total)}` : 'Unavailable' }}</span>
        </div>
      </div>
      <div class="flex min-w-0 flex-col gap-1">
        <span class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ eyebrow }}</span>
        <span class="text-lg font-semibold">{{ title }}</span>
        <span v-if="meta" class="text-sm text-muted-foreground">{{ meta }}</span>
        <button v-if="actionLabel" type="button" class="self-start text-sm font-medium hover:underline" @click="$emit('open')">{{ actionLabel }}</button>
      </div>
    </CardContent>
  </Card>
</template>
