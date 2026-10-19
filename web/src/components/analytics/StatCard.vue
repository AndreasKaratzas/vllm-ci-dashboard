<script setup lang="ts">
import { computed } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { StripItem } from '@/domain/analytics'
import { cn } from '@/lib/utils'

// One classic status-strip stat as a dashboard KPI card. Links open their
// source; otherwise the whole card opens the drill-down unless it is static.
const props = defineProps<{ item: StripItem; badge?: string; action?: string }>()
defineEmits<{ open: [] }>()

const ACCENT: Record<string, string> = {
  pass: 'before:bg-success',
  hard: 'before:bg-destructive',
  soft: 'before:bg-warning',
  info: 'before:bg-info',
  neutral: 'before:bg-muted-foreground/50',
}
const classes = computed(() =>
  cn(
    '@container/card relative h-full overflow-hidden text-left before:absolute before:inset-x-0 before:top-0 before:h-1',
    ACCENT[props.item.tone] ?? 'before:bg-transparent',
    !props.item.static && 'transition-colors hover:bg-accent/40',
  ),
)
const tag = computed(() => (props.item.url ? 'a' : props.item.static ? 'div' : 'button'))
</script>

<template>
  <component
    :is="tag"
    :href="item.url || undefined"
    :target="item.url ? '_blank' : undefined"
    :rel="item.url ? 'noopener' : undefined"
    :type="tag === 'button' ? 'button' : undefined"
    class="block h-full rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    :data-tone="item.tone || 'none'"
    @click="tag === 'button' && $emit('open')"
  >
    <Card :class="classes">
      <CardHeader>
        <CardDescription class="text-xs font-semibold tracking-wide uppercase" data-part="label">{{ item.label }}</CardDescription>
        <CardTitle
          class="font-semibold tabular-nums"
          :class="item.value.length > 12 ? 'text-xl @[250px]/card:text-2xl' : 'text-2xl @[250px]/card:text-3xl'"
          data-part="value"
          >{{ item.value }}</CardTitle
        >
        <CardAction v-if="badge">
          <ToneBadge :tone="item.tone || 'neutral'">{{ badge }}</ToneBadge>
        </CardAction>
      </CardHeader>
      <CardFooter class="flex-col items-start gap-1.5 text-sm">
        <div class="text-muted-foreground" data-part="meta">{{ item.meta }}</div>
        <div v-if="action || item.actionLabel" class="font-medium">{{ action || item.actionLabel }}</div>
      </CardFooter>
    </Card>
  </component>
</template>
