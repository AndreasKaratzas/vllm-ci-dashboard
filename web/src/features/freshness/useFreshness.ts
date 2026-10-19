import { createSharedComposable } from '@vueuse/core'
import { computed } from 'vue'
import { useClock } from '@/composables/useClock'
import { useOperations } from '@/composables/useOperations'
import { useSheetStack } from '@/composables/useSheetStack'
import { coreFreshness, freshnessRows, queueFreshness } from '@/domain/freshness'
import { freshnessDetail } from './freshness'

/** Source freshness from the manifest shell, plus the one "Data freshness" sheet the header renders. */
export const useFreshness = createSharedComposable(() => {
  const { ops } = useOperations()
  const now = useClock()
  const sheets = useSheetStack()
  const rows = computed(() => (ops.value ? freshnessRows(ops.value, null, now.value) : []))
  const core = computed(() => (rows.value.length ? coreFreshness(rows.value) : null))
  const queue = computed(() => (rows.value.length ? queueFreshness(rows.value) : null))
  return {
    rows,
    core,
    queue,
    now,
    sheets,
    open: () => sheets.open(freshnessDetail(rows.value, now.value)),
  }
})
