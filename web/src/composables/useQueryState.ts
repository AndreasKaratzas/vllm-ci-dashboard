import { computed, type WritableComputedRef } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'

export interface QueryStateOptions<T extends string> {
  default: T
  /** Values accepted from the URL; anything else reads as the default. */
  allowed?: readonly T[]
  /** 'push' adds a history entry per change; 'replace' (default) rewrites the current one. */
  history?: 'push' | 'replace'
}

/** One URL query parameter as a writable value; the default is kept out of the URL. */
export function useQueryState<T extends string>(key: string, options: QueryStateOptions<T>): WritableComputedRef<T> {
  const route = useRoute()
  const router = useRouter()
  const accepts = (value: unknown): value is T =>
    typeof value === 'string' && value !== '' && (!options.allowed || options.allowed.includes(value as T))
  return computed({
    get() {
      const raw = route.query[key]
      const value = Array.isArray(raw) ? raw[0] : raw
      return accepts(value) ? value : options.default
    },
    set(value: T) {
      const query: LocationQuery = { ...route.query }
      if (value === options.default || !accepts(value)) delete query[key]
      else query[key] = value
      void (options.history === 'push' ? router.push({ query }) : router.replace({ query }))
    },
  })
}
