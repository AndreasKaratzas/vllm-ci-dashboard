<script setup lang="ts">
import { TrendingDown, TrendingUp } from '@lucide/vue'
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { Tone } from '@/domain/format'
import ToneBadge from './ToneBadge.vue'

defineProps<{
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
</script>

<template>
  <Card class="@container/card">
    <CardHeader>
      <CardDescription>{{ label }}</CardDescription>
      <CardTitle
        class="font-semibold tabular-nums"
        :class="value.length > 10 ? 'text-xl @[250px]/card:text-2xl' : 'text-2xl @[250px]/card:text-3xl'"
        >{{ value }}</CardTitle
      >
      <CardAction v-if="badge">
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
