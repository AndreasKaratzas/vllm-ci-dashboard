<script setup lang="ts">
import { computed } from 'vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, type Tone } from '@/domain/format'

export interface Segment {
  label: string
  count: number
  tone: Tone
  key?: string
}
const props = defineProps<{ title: string; subtitle?: string; segments: Segment[] }>()
defineEmits<{ open: [Segment] }>()

const total = computed(() => props.segments.reduce((sum, s) => sum + Number(s.count || 0), 0))
</script>

<script lang="ts">
export const SEGMENT_FILL: Record<Tone, string> = {
  'is-success': 'bg-success',
  'is-danger': 'bg-destructive',
  'is-warning': 'bg-warning',
  'is-info': 'bg-info',
  'is-neutral': 'bg-muted-foreground',
  'is-not-targeted': 'bg-muted-foreground/40',
}
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ title }}</CardTitle>
      <CardDescription v-if="subtitle">{{ subtitle }}</CardDescription>
    </CardHeader>
    <CardContent class="flex flex-col gap-4">
      <div class="flex h-3 w-full overflow-hidden rounded-full bg-muted" role="img" :aria-label="segments.map((s) => `${s.label}: ${s.count}`).join(', ')">
        <span
          v-for="segment in segments.filter((s) => s.count > 0)"
          :key="segment.label"
          :class="SEGMENT_FILL[segment.tone]"
          :style="{ width: `${(segment.count / Math.max(1, total)) * 100}%` }"
          :title="`${segment.label}: ${integer(segment.count)}`"
        />
      </div>
      <div class="grid gap-2 sm:grid-cols-3">
        <button
          v-for="segment in segments"
          :key="segment.label"
          type="button"
          class="flex items-center gap-2 rounded-md border px-3 py-2 text-left text-sm hover:bg-accent"
          @click="$emit('open', segment)"
        >
          <span class="size-2.5 shrink-0 rounded-full" :class="SEGMENT_FILL[segment.tone]" />
          <strong class="tabular-nums">{{ integer(segment.count) }}</strong>
          <span class="text-muted-foreground">{{ segment.label }}</span>
        </button>
      </div>
    </CardContent>
  </Card>
</template>
