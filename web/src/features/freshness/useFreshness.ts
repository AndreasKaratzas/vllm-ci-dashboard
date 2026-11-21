import { createSharedComposable } from '@vueuse/core'
import { computed, getCurrentScope, shallowRef, toValue, watch, type MaybeRefOrGetter } from 'vue'
import type { Freshness } from '@/components/layout/PageHeader.vue'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useOperations } from '@/composables/useOperations'
import { useSheetStack } from '@/composables/useSheetStack'
import { coreFreshness, freshnessOf, freshnessRows, queueFreshness } from '@/domain/freshness'
import type { Rec } from '@/domain/format'
import { freshnessChip, freshnessDetail } from './freshness'

/** Source freshness from the manifest shell, plus the one "Data freshness" sheet the header renders. */
export const useFreshness = createSharedComposable(() => {
  const { ops } = useOperations()
  const now = useClock()
  const sheets = useSheetStack()
  // A manifest written before perf_eval joined shell.sources dates it from perf_eval.json itself.
  const scope = getCurrentScope()
  const perf = shallowRef<Rec | null>(null)
  let perfLoaded = false
  watch(
    () => !!ops.value && !('perf_eval' in (ops.value.sources ?? {})),
    (needed) => {
      if (!needed || perfLoaded || !scope) return
      perfLoaded = true
      scope.run(() => {
        const dataset = useDataset('perf_eval')
        watch(dataset.data, (payload) => (perf.value = payload), { immediate: true })
      })
    },
    { immediate: true },
  )
  const rows = computed(() => (ops.value ? freshnessRows(ops.value, perf.value, now.value) : []))
  const core = computed(() => (rows.value.length ? coreFreshness(rows.value) : null))
  const queue = computed(() => (rows.value.length ? queueFreshness(rows.value) : null))
  /** The PageHeader chip of the named sources, the one rule every page uses. */
  const chip = (keys: MaybeRefOrGetter<readonly string[]>, prefix?: string) =>
    computed<Freshness | undefined>(() => (rows.value.length ? freshnessChip(freshnessOf(rows.value, toValue(keys)), now.value, prefix) : undefined))
  return {
    rows,
    core,
    queue,
    now,
    sheets,
    chip,
    open: () => sheets.open(freshnessDetail(rows.value, now.value)),
  }
})
