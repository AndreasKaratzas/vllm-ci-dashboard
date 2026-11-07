import { computed } from 'vue'
import { useOperationSections, useOperations } from '@/composables/useOperations'
import { mainBuilds, mainCiRecord, mainCiSummary, mainIncidents } from '@/domain/mainCi'
import type { MainCiContext } from './details'

/**
 * The main CI section with the manifest shell's headline as its stand-in until it loads. A
 * manifest without a main_ci entry (bundle v3) is "unpublished", not an error.
 */
export function useMainCi() {
  const { ops, error: shellError, retry: shellRetry } = useOperations()
  const section = useOperationSections(['main_ci'])
  const published = computed(() => !!ops.value && !!mainCiRecord(ops.value))
  const record = computed(() => mainCiRecord(section.data.value) ?? mainCiRecord(ops.value))
  const summary = computed(() => mainCiSummary(record.value))
  const builds = computed(() => mainBuilds(section.data.value?.main_ci))
  const incidents = computed(() => mainIncidents(section.data.value?.main_ci))
  const ctx = computed<MainCiContext>(() => ({ record: section.data.value?.main_ci ?? null, builds: builds.value }))
  /** The full section (series, builds, incidents) has loaded; until then only the headline is known. */
  const loaded = computed(() => !!section.data.value?.main_ci)
  const error = computed(() => (published.value ? section.error.value : null))
  return { ops, shellError, shellRetry, published, record, summary, builds, incidents, ctx, loaded, loading: section.loading, error, retry: section.retry }
}

export type MainCi = ReturnType<typeof useMainCi>
