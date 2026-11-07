import { shallowRef } from 'vue'
import type { Detail } from '@/components/evidence/detail'

/** Drill-down navigation: each pushed Detail opens above the previous one, Back pops it. */
export function useSheetStack() {
  const stack = shallowRef<Detail[]>([])
  return {
    stack,
    open(detail: Detail) {
      stack.value = [detail]
    },
    push(detail: Detail) {
      stack.value = [...stack.value, detail]
    },
    closeFrom(index: number) {
      stack.value = stack.value.slice(0, Math.max(0, index))
    },
    close() {
      stack.value = []
    },
  }
}

export type SheetStack = ReturnType<typeof useSheetStack>
