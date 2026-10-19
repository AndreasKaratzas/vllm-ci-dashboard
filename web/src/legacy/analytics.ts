// Maps classic ?ops_analytics_view=... links to v2 CI Analytics routes.
// Add a sub-view here once it is ported; unported views stay on classic.html.
const PORTED_VIEWS: Record<string, string> = {}

export function analyticsRoute(params: URLSearchParams): string | null {
  const view = params.get('ops_analytics_view') || 'groups'
  return PORTED_VIEWS[view] ?? null
}
