<script setup lang="ts">
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { Tone } from '@/domain/format'
import ToneBadge from './ToneBadge.vue'

defineProps<{ label: string; value: string; meta?: string; tone: Tone; href?: string; actionLabel?: string }>()
defineEmits<{ open: [] }>()
</script>

<template>
  <Card class="gap-3">
    <CardHeader>
      <CardTitle class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ label }}</CardTitle>
    </CardHeader>
    <CardContent class="flex flex-col gap-2">
      <div><ToneBadge :tone="tone" class="text-sm">{{ value }}</ToneBadge></div>
      <p v-if="meta" class="text-sm text-muted-foreground">{{ meta }}</p>
      <a v-if="href" :href="href" target="_blank" rel="noopener" class="text-sm font-medium text-primary hover:underline">
        {{ actionLabel || 'Open' }}
      </a>
      <button v-else-if="actionLabel" type="button" class="self-start text-sm font-medium text-primary hover:underline" @click="$emit('open')">
        {{ actionLabel }}
      </button>
    </CardContent>
  </Card>
</template>
