// Maps classic ?ops_analytics_view=... links to v2 CI Analytics routes.
// Add a sub-view here once it is ported; unported views stay on classic.html.
const PORTED_VIEWS: Record<string, string> = {
  dns: '#/analytics?view=dns',
  'agent-health': '#/analytics?view=agent-health',
}

// Classic route state carried by the ported views: [classic param, v2 param, accepted values, default].
type ParamSpec = [string, string, string[] | null, string]
const VIEW_PARAMS: Record<string, ParamSpec[]> = {
  dns: [
    ['ops_analytics_dns_window', 'dns_window', ['1h', '3h', '12h', '24h', '72h', '168h', '720h'], '24h'],
    ['ops_analytics_dns_scope', 'dns_scope', ['canonical', 'amd'], 'amd'],
  ],
  'agent-health': [
    ['ops_agent_window', 'agent_window', ['1d', '3d', '7d', '14d', '30d', '60d'], '7d'],
    ['ops_agent_gpu', 'agent_gpu', null, 'all'],
    ['ops_agent_node', 'agent_node', null, ''],
    ['ops_agent_cofail', 'agent_cofail', ['30', '60', '120', '180', '360', '720', '1440'], '180'],
    ['ops_agent_excl_cancel', 'agent_excl_cancel', ['0', '1'], '1'],
    ['ops_agent_nightly', 'agent_nightly', ['0', '1'], '0'],
    ['ops_agent_signal', 'agent_signal', ['infra', 'hard', 'all'], 'infra'],
  ],
}

function withViewParams(view: string, route: string, params: URLSearchParams): string {
  const extra = new URLSearchParams()
  for (const [legacy, name, allowed, fallback] of VIEW_PARAMS[view] ?? []) {
    const raw = params.get(legacy)
    // Classic drops unknown values back to the default; defaults stay implicit.
    if (raw && (!allowed || allowed.includes(raw)) && raw !== fallback) extra.set(name, raw)
  }
  const query = extra.toString()
  return query ? `${route}&${query}` : route
}

export function analyticsRoute(params: URLSearchParams): string | null {
  // Classic opens AMD test health whenever a test-group search is present.
  const view = params.get('ops_analytics_search') !== null ? 'groups' : params.get('ops_analytics_view') || 'groups'
  const route = PORTED_VIEWS[view]
  if (!route) return null
  return withViewParams(view, route, params)
}

/** The retired Queue tab's DNS view (dashboard-nav.js migrates it to CI Analytics); other Queue views stay classic. */
export function queueRoute(params: URLSearchParams): string | null {
  if (params.get('ops_queue_view') !== 'dns') return null
  const migrated = new URLSearchParams({ ops_analytics_view: 'dns' })
  const dnsWindow = params.get('ops_queue_dns_window')
  if (['1h', '3h', '12h', '72h', '168h', '720h'].includes(dnsWindow ?? '')) migrated.set('ops_analytics_dns_window', dnsWindow!)
  if (params.get('ops_queue_scope') === 'canonical') migrated.set('ops_analytics_dns_scope', 'canonical')
  return analyticsRoute(migrated)
}
