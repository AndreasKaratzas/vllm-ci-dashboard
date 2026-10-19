<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import type { Detail } from '@/components/evidence/detail'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import type { CellSpec } from './columns'

const props = defineProps<{ cell: CellSpec }>()
const emit = defineEmits<{ drill: [Detail] }>()

function open() {
  if (props.cell.detail) emit('drill', props.cell.detail())
  else props.cell.onOpen?.()
}
</script>

<template>
  <span class="inline-flex min-w-0 flex-col gap-0.5" data-slot="cell">
    <a
      v-if="cell.href"
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
    <span v-else :class="[cell.mono && 'font-mono text-xs', cell.text === '—' && 'text-muted-foreground']">{{ cell.text }}</span>
    <Sparkline v-if="cell.spark" :values="cell.spark.values" :label="cell.spark.label" :color="cell.spark.color" class="h-8! w-28" />
    <span v-if="cell.sub" class="text-xs text-muted-foreground">{{ cell.sub }}</span>
  </span>
</template>
