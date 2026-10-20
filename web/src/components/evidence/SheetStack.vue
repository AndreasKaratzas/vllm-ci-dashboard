<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import type { SheetStack } from '@/composables/useSheetStack'
import type { Detail } from './detail'
import EvidenceSheet from './EvidenceSheet.vue'

const props = defineProps<{ sheets: SheetStack }>()

interface Entry {
  key: string
  detail: Detail
  depth: number
  open: boolean
}
const keyOf = (detail: Detail, depth: number) => `${depth}-${detail.id}`

// Popped sheets stay mounted closed until their exit animation ends.
const leaving = shallowRef<Entry[]>([])
watch(
  () => props.sheets.stack.value,
  (next, prev = []) => {
    const live = new Set(next.map(keyOf))
    const popped = prev.flatMap((detail, depth) => (next[depth] === detail ? [] : [{ key: keyOf(detail, depth), detail, depth, open: false }]))
    leaving.value = [...leaving.value, ...popped].filter((entry) => !live.has(entry.key))
  },
)
const WIDTHS = ['md', 'lg', 'xl'] as const
// The widest sheet at or below each depth, so a drill-down never shows a wider panel behind it.
function floorWidth(depth: number): Detail['width'] {
  const widths = props.sheets.stack.value.slice(0, depth).map((detail) => WIDTHS.indexOf(detail.width ?? 'lg'))
  return widths.length ? WIDTHS[Math.max(...widths)] : undefined
}
const entries = computed<Entry[]>(() =>
  [...props.sheets.stack.value.map((detail, depth) => ({ key: keyOf(detail, depth), detail, depth, open: true })), ...leaving.value].sort(
    (a, b) => a.depth - b.depth,
  ),
)
function forget(key: string) {
  leaving.value = leaving.value.filter((entry) => entry.key !== key)
}
</script>

<template>
  <EvidenceSheet
    v-for="entry in entries"
    :key="entry.key"
    :detail="entry.detail"
    :depth="entry.depth"
    :min-width="floorWidth(entry.depth)"
    :open="entry.open"
    @update:open="(open: boolean) => !open && entry.open && sheets.closeFrom(entry.depth)"
    @drill="sheets.push"
    @closed="forget(entry.key)"
  />
</template>
