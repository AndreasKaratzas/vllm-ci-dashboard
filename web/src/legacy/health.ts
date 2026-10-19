// Maps classic ?ops_health_view=... links to v2 CI Health routes.
export function healthRoute(params: URLSearchParams): string | null {
  const view = params.get('ops_health_view')
  // 'targets' was retired into Overview (classic dashboard-nav.js does the same).
  if (!view || view === 'overview' || view === 'targets') return '#/health'
  if (view === 'parity') return '#/health?view=parity'
  return null
}
