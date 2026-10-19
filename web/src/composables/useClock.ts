import { createSharedComposable, useIntervalFn } from '@vueuse/core'
import { readonly, shallowRef, type DeepReadonly, type ShallowRef } from 'vue'

const CLOCK_TICK_MS = 30_000

/** Current time in ms, shared by every caller and ticking every 30 s, so "x ago" labels stay current. */
export const useClock = createSharedComposable((): DeepReadonly<ShallowRef<number>> => {
  const now = shallowRef(Date.now())
  useIntervalFn(() => {
    now.value = Date.now()
  }, CLOCK_TICK_MS)
  return readonly(now)
})
