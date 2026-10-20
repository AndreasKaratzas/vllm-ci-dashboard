<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import type { Detail } from '@/components/evidence/detail'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import { Badge } from '@/components/ui/badge'
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
      :class="[cell.mono && 'font-mono text-xs', cell.muted && 'text-muted-foreground']"
      @click="open"
    >
      <StatusBadge v-if="cell.tone" :tone="cell.tone" :label="cell.text" />
      <template v-else>{{ cell.text }}</template>
    </button>
    <StatusBadge v-else-if="cell.tone" :tone="cell.tone" :label="cell.text" />
    <span v-else-if="cell.tags" class="flex flex-wrap gap-1">
      <Badge v-for="tag in cell.tags" :key="tag" variant="outline" class="font-normal text-muted-foreground">{{ tag }}</Badge>
      <span v-if="!cell.tags.length" class="text-muted-foreground">—</span>
    </span>
    <span v-else-if="cell.dots?.length" class="inline-flex items-center gap-1.5" :class="[cell.mono && 'font-mono text-xs', cell.nowrap && 'sm:whitespace-nowrap']">
      <span class="inline-flex shrink-0 -space-x-0.5" aria-hidden="true">
        <span v-for="(color, index) in cell.dots" :key="index" class="size-2 rounded-full ring-2 ring-card" :style="{ backgroundColor: color }" />
      </span>
      {{ cell.text }}
    </span>
    <span v-else :class="[cell.mono && 'font-mono text-xs', (cell.muted || cell.text === '—') && 'text-muted-foreground', cell.nowrap && 'sm:whitespace-nowrap']">{{ cell.text }}</span>
    <Sparkline v-if="cell.spark" :values="cell.spark.values" :label="cell.spark.label" :color="cell.spark.color" class="h-8! w-28" />
    <span v-if="cell.sub" class="text-xs text-muted-foreground">{{ cell.sub }}</span>
  </span>
</template>
