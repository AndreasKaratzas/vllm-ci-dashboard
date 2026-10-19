<script setup lang="ts">
import { Minus, TrendingDown, TrendingUp } from '@lucide/vue'
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import { toneClasses } from '@/lib/palette'

export interface Delta {
  value: number
  /** Which direction is an improvement; omitted when the change is neither good nor bad. */
  goodWhen?: 'up' | 'down'
  text?: string
}

const props = defineProps<{ delta: Delta }>()

const direction = computed(() => (props.delta.value > 0 ? 'up' : props.delta.value < 0 ? 'down' : 'flat'))
const text = computed(() => props.delta.text ?? `${props.delta.value > 0 ? '+' : ''}${props.delta.value}`)
const worse = computed(() => props.delta.goodWhen !== undefined && direction.value !== 'flat' && direction.value !== props.delta.goodWhen)
const icon = computed(() => ({ up: TrendingUp, down: TrendingDown, flat: Minus })[direction.value])
const description = computed(() => {
  const quality = props.delta.goodWhen === undefined || direction.value === 'flat' ? '' : worse.value ? ', worse' : ', better'
  return `${direction.value === 'flat' ? 'unchanged' : direction.value} ${text.value}${quality}`
})
</script>

<template>
  <Badge variant="outline" class="gap-1 font-medium tabular-nums" :class="worse && toneClasses('hard', 'text')" :aria-label="description" :data-direction="direction">
    <component :is="icon" aria-hidden="true" />
    {{ text }}
  </Badge>
</template>
