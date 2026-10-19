import { OMNI_ROUTE_DEFAULTS, omniRouteValue } from '@/domain/omni'

// Maps classic ?ops_omni_mapping_range=...&ops_omni_range=... links to the v2
// Omni CI route. Invalid values fall back to the defaults, as classic does;
// ops_omni_age is not read by the classic page and is dropped.
export function omniRoute(params: URLSearchParams): string | null {
  const query = new URLSearchParams()
  const mapping = omniRouteValue('omni_mapping_range', params.get('ops_omni_mapping_range'))
  const range = omniRouteValue('omni_range', params.get('ops_omni_range'))
  if (mapping !== OMNI_ROUTE_DEFAULTS.omni_mapping_range) query.set('mapping', mapping)
  if (range !== OMNI_ROUTE_DEFAULTS.omni_range) query.set('range', range)
  const search = query.toString()
  return '#/omni' + (search ? '?' + search : '')
}
