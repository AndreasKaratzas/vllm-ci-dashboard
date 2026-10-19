import { useDataset } from '@/composables/useDataset'

/** DNS observations, arbitrated between the live branch and Pages and refreshed while visible. */
export function useDnsData() {
  const { data, error, loading, refresh } = useDataset('dns')
  return { payload: data, error, loading, retry: refresh }
}
