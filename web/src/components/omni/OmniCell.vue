<script setup lang="ts">
import ToneBadge from '@/components/ops/ToneBadge.vue'
import type { Cell, OmniDetail } from '@/domain/omni'

defineProps<{ cell: Cell }>()
const emit = defineEmits<{ open: [OmniDetail] }>()
</script>

<template>
  <a v-if="cell.tone && cell.href" :href="cell.href" target="_blank" rel="noopener" class="inline-flex">
    <ToneBadge :tone="cell.tone">{{ cell.text }}</ToneBadge>
  </a>
  <button v-else-if="cell.tone && cell.open" type="button" class="inline-flex" @click="emit('open', cell.open())">
    <ToneBadge :tone="cell.tone">{{ cell.text }}</ToneBadge>
  </button>
  <ToneBadge v-else-if="cell.tone" :tone="cell.tone">{{ cell.text }}</ToneBadge>
  <a v-else-if="cell.href" :href="cell.href" target="_blank" rel="noopener" class="font-medium hover:underline" :class="{ 'font-mono text-xs': cell.mono }">{{ cell.text }}</a>
  <button
    v-else-if="cell.open"
    type="button"
    class="text-left font-medium underline decoration-muted-foreground/50 decoration-dotted underline-offset-4 hover:decoration-solid"
    :class="{ 'font-mono text-xs': cell.mono }"
    @click="emit('open', cell.open())"
  >
    {{ cell.text }}
  </button>
  <span v-else :class="{ 'font-mono text-xs': cell.mono }">{{ cell.text }}</span>
</template>
