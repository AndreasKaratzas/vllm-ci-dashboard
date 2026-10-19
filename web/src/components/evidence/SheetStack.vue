<script setup lang="ts">
import type { SheetStack } from '@/composables/useSheetStack'
import EvidenceSheet from './EvidenceSheet.vue'

defineProps<{ sheets: SheetStack }>()
</script>

<template>
  <EvidenceSheet
    v-for="(detail, index) in sheets.stack.value"
    :key="`${index}-${detail.id}`"
    :detail="detail"
    :depth="index"
    :open="true"
    @update:open="(open: boolean) => !open && sheets.closeFrom(index)"
    @drill="sheets.push"
  />
</template>
