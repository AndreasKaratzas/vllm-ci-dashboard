<script setup lang="ts">
import { TrendingDown, TrendingUp } from '@lucide/vue'
import { computed } from 'vue'
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { Tone } from '@/domain/format'
import ToneBadge from './ToneBadge.vue'

const props = defineProps<{
  label: string
  value: string
  badge?: string
  badgeTone?: Tone
  trend?: 'up' | 'down' | null
  headline?: string
  detail?: string
  href?: string
}>()
defineEmits<{ open: [] }>()

// A long value beside a long badge wraps word by word in the header's action
// column, so long content stacks the badge above the value instead.
const stacked = computed(() => props.value.length > 10 || (props.badge?.length ?? 0) > 14)
</script>

<template>
  <Card class="@container/card">
    <CardHeader>
      <CardDescription>{{ label }}</CardDescription>
      <div v-if="badge && stacked">
        <ToneBadge :tone="badgeTone ?? 'is-neutral'">
          <TrendingUp v-if="trend === 'up'" />
          <TrendingDown v-else-if="trend === 'down'" />
          {{ badge }}
        </ToneBadge>
      </div>
      <CardTitle
        class="font-semibold tabular-nums"
        :class="value.length > 10 ? 'text-xl @[250px]/card:text-2xl' : 'text-2xl @[250px]/card:text-3xl'"
        >{{ value }}</CardTitle
      >
      <CardAction v-if="badge && !stacked">
        <ToneBadge :tone="badgeTone ?? 'is-neutral'">
          <TrendingUp v-if="trend === 'up'" />
          <TrendingDown v-else-if="trend === 'down'" />
          {{ badge }}
        </ToneBadge>
      </CardAction>
    </CardHeader>
    <CardFooter class="flex-col items-start gap-1.5 text-sm">
      <a v-if="href" :href="href" target="_blank" rel="noopener" class="line-clamp-1 font-medium hover:underline">{{ headline }} ↗</a>
      <button v-else-if="headline" type="button" class="line-clamp-1 text-left font-medium hover:underline" @click="$emit('open')">
        {{ headline }}
      </button>
      <div v-if="detail" class="text-muted-foreground">{{ detail }}</div>
    </CardFooter>
  </Card>
</template>
