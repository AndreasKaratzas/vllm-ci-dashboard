// Maps classic ?ops_perf_view=...&ops_perf_model=...&ops_perf_device=... links to the v2 Perf Eval route.
// Values are read like classic syncRouteState; ops_detail is written by classic but never restored.
import { perfQuery, perfState } from '@/domain/perf'

export function perfRoute(params: URLSearchParams): string | null {
  const query = new URLSearchParams(
    perfQuery(
      perfState({ view: params.get('ops_perf_view'), model: params.get('ops_perf_model'), device: params.get('ops_perf_device') }),
    ),
  ).toString()
  return query ? `#/perf?${query}` : '#/perf'
}
