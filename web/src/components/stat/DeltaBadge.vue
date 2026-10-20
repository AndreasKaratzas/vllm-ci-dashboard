<script setup lang="ts">
import { Minus, TrendingDown, TrendingUp } from '@lucide/vue'
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import { toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

export interface Delta {
  value: number
  /** Which direction is an improvement; omitted when the change is neither good nor bad. */
  goodWhen?: 'up' | 'down'
  text?: string
  /** A verdict decided elsewhere (e.g. a threshold): 'hard' is worse, 'pass' is better, anything else is neither. */
  tone?: Tone
  /** The baseline, e.g. 'vs the previous nightly'; shown as the tooltip and read out. */
  label?: string
}

const props = defineProps<{ delta: Delta }>()

const direction = computed(() => (props.delta.value > 0 ? 'up' : props.delta.value < 0 ? 'down' : 'flat'))
const text = computed(() => props.delta.text ?? `${props.delta.value > 0 ? '+' : ''}${props.delta.value}`)
const verdict = computed<'worse' | 'better' | null>(() => {
  if (props.delta.tone !== undefined) return props.delta.tone === 'hard' ? 'worse' : props.delta.tone === 'pass' ? 'better' : null
  if (props.delta.goodWhen === undefined || direction.value === 'flat') return null
  return direction.value === props.delta.goodWhen ? 'better' : 'worse'
})
const icon = computed(() => ({ up: TrendingUp, down: TrendingDown, flat: Minus })[direction.value])
const description = computed(() => {
  const baseline = props.delta.label ? ` ${props.delta.label}` : ''
  return `${direction.value === 'flat' ? 'unchanged' : direction.value} ${text.value}${baseline}${verdict.value ? `, ${verdict.value}` : ''}`
})
// A threshold verdict tints regressions and colors improvements; a plain worse direction only colors the text.
const classes = computed(() => {
  if (props.delta.tone === undefined) return verdict.value === 'worse' && toneClasses('hard', 'text')
  if (verdict.value === 'worse') return toneClasses('hard', 'badge')
  return verdict.value === 'better' ? toneClasses('pass', 'text') : 'text-muted-foreground'
})
</script>

<template>
  <Badge
    variant="outline"
    class="gap-1 font-medium tabular-nums"
    :class="classes"
    :aria-label="description"
    :title="delta.label"
    :data-direction="direction"
    :data-verdict="verdict ?? undefined"
  >
    <component :is="icon" aria-hidden="true" />
    {{ text }}
  </Badge>
</template>
