<script setup lang="ts">
// Required / optional / soft-fail share of the published mirror groups.
defineProps<{ segments: { key: string; label: string; count: number; width: number }[]; ariaLabel?: string }>()
</script>

<script lang="ts">
export const MODE_FILL: Record<string, string> = {
  required: 'bg-chart-2',
  optional: 'bg-info',
  soft_fail: 'bg-warning',
}
</script>

<template>
  <div class="flex h-2.5 w-full overflow-hidden rounded-full bg-muted" role="img" :aria-label="ariaLabel" data-testid="mirror-mode-track">
    <span
      v-for="segment in segments"
      :key="segment.key"
      :class="MODE_FILL[segment.key]"
      :style="{ width: `${segment.width}%` }"
      :title="`${segment.label}: ${segment.count}`"
    />
  </div>
</template>
