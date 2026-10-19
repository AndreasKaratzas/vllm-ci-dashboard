<script setup lang="ts">
import { Minus, TrendingDown, TrendingUp } from '@lucide/vue'
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import type { PerfDelta } from '@/domain/perf'
import { toneClasses } from '@/lib/palette'
import { cn } from '@/lib/utils'

const props = defineProps<{ delta: PerfDelta }>()

// Pass stays quiet (colored text only); a regression gets the tinted badge.
const CLASSES: Partial<Record<string, string>> = {
  pass: `border-border ${toneClasses('pass', 'text')}`,
  hard: toneClasses('hard', 'badge'),
}
const icon = computed(() => (props.delta.label.startsWith('+') ? TrendingUp : props.delta.label.startsWith('-') ? TrendingDown : Minus))
</script>

<template>
  <Badge
    variant="outline"
    :class="cn('gap-1 font-medium tabular-nums', CLASSES[delta.tone] ?? 'text-muted-foreground')"
    :title="delta.title ?? undefined"
    :data-tone="delta.tone"
  >
    <component :is="icon" aria-hidden="true" />{{ delta.label }}
  </Badge>
</template>
