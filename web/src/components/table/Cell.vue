<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import type { CellSpec } from './columns'

const props = defineProps<{ cell: CellSpec }>()
const emit = defineEmits<{ drill: [Detail] }>()

function open() {
  if (props.cell.detail) emit('drill', props.cell.detail())
  else props.cell.onOpen?.()
}
const barWidth = computed(() => {
  const bar = props.cell.bar
  return bar && bar.max > 0 ? `${Math.min(100, (Math.max(0, bar.value) / bar.max) * 100)}%` : '0%'
})
</script>

<template>
  <span class="inline-flex min-w-0 flex-col gap-0.5" :class="cell.bar && 'w-full'" data-slot="cell">
    <span v-if="cell.bar" class="flex items-center justify-end gap-3">
      <span class="hidden h-1.5 w-full max-w-28 overflow-hidden rounded-full bg-muted sm:block" aria-hidden="true" data-slot="cell-bar">
        <span class="block h-full rounded-full motion-safe:transition-[width] motion-safe:duration-500" :style="{ width: barWidth, backgroundColor: cell.bar.color }" />
      </span>
      <span class="shrink-0">{{ cell.text }}</span>
    </span>
    <a
      v-else-if="cell.href"
      :href="cell.href"
      target="_blank"
      rel="noopener"
      class="relative z-10 inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline"
      :class="cell.mono && 'font-mono text-xs'"
    >
      <StatusBadge v-if="cell.tone" :tone="cell.tone" :label="cell.text" />
      <template v-else>{{ cell.text }}</template>
      <ExternalLink class="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
    </a>
    <button
      v-else-if="cell.detail || cell.onOpen"
      type="button"
      class="relative z-10 inline-flex items-center text-left font-medium underline decoration-muted-foreground/50 decoration-dotted underline-offset-4 hover:decoration-solid"
      :class="cell.mono && 'font-mono text-xs'"
      @click="open"
    >
      <StatusBadge v-if="cell.tone" :tone="cell.tone" :label="cell.text" />
      <template v-else>{{ cell.text }}</template>
    </button>
    <StatusBadge v-else-if="cell.tone" :tone="cell.tone" :label="cell.text" />
    <span v-else :class="[cell.mono && 'font-mono text-xs', cell.text === '—' && 'text-muted-foreground']" :title="cell.title">{{ cell.text }}</span>
    <span v-if="cell.sub" class="text-xs text-muted-foreground">{{ cell.sub }}</span>
  </span>
</template>
