// Old dashboard links (issue bodies, watchers, bookmarks) use ?ops_<state>=...#<tab>.
// This is the one table that maps them to routes; anything without a route still
// opens the old page, which serves the views that have no route yet.
import { OMNI_ROUTE_DEFAULTS, omniRouteValue } from '@/domain/omni'
import { perfQuery, perfState } from '@/domain/perf'

export const OLD_PAGE = 'classic.html'

// ---- CI Health: ?ops_health_view=...
export function healthRoute(params: URLSearchParams): string | null {
  const view = params.get('ops_health_view')
  // 'targets' was retired into Overview.
  if (!view || view === 'overview' || view === 'targets') return '#/health'
  if (view === 'parity') return '#/health?view=parity'
  if (view === 'mirrors') return '#/health?view=mirrors'
  if (view === 'coverage') {
    // platform | name | area, falling back to platform.
    const sort = params.get('ops_health_sort')
    return sort === 'name' || sort === 'area' ? `#/health?view=coverage&sort=${sort}` : '#/health?view=coverage'
  }
  return null
}

// ---- CI Analytics: ?ops_analytics_view=...
// Sub-views with a route; any other view opens the old page.
const PORTED_VIEWS: Record<string, string> = {
  groups: '#/analytics',
  latency: '#/analytics?view=latency',
  nightlies: '#/analytics?view=nightlies',
  dns: '#/analytics?view=dns',
  'agent-health': '#/analytics?view=agent-health',
}

// State carried into the routed views: [old param, route param, accepted values, default].
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
  for (const [old, name, allowed, fallback] of VIEW_PARAMS[view] ?? []) {
    const raw = params.get(old)
    // Unknown values fall back to the default; defaults stay implicit.
    if (raw && (!allowed || allowed.includes(raw)) && raw !== fallback) extra.set(name, raw)
  }
  const query = extra.toString()
  return query ? `${route}&${query}` : route
}

export function analyticsRoute(params: URLSearchParams): string | null {
  // A test-group search always opened AMD test health.
  const view = params.get('ops_analytics_search') !== null ? 'groups' : params.get('ops_analytics_view') || 'groups'
  const route = PORTED_VIEWS[view]
  if (!route) return null
  return withViewParams(view, route, params)
}

/** The retired Queue tab's DNS view, now in CI Analytics; other Queue views open the old page. */
export function queueRoute(params: URLSearchParams): string | null {
  if (params.get('ops_queue_view') !== 'dns') return null
  const migrated = new URLSearchParams({ ops_analytics_view: 'dns' })
  const dnsWindow = params.get('ops_queue_dns_window')
  if (['1h', '3h', '12h', '72h', '168h', '720h'].includes(dnsWindow ?? '')) migrated.set('ops_analytics_dns_window', dnsWindow!)
  if (params.get('ops_queue_scope') === 'canonical') migrated.set('ops_analytics_dns_scope', 'canonical')
  return analyticsRoute(migrated)
}

// ---- Omni CI
// ?ops_omni_mapping_range=...&ops_omni_range=...; invalid values fall back to the
// defaults and ops_omni_age, which the old page never read, is dropped.
export function omniRoute(params: URLSearchParams): string | null {
  const query = new URLSearchParams()
  const mapping = omniRouteValue('omni_mapping_range', params.get('ops_omni_mapping_range'))
  const range = omniRouteValue('omni_range', params.get('ops_omni_range'))
  if (mapping !== OMNI_ROUTE_DEFAULTS.omni_mapping_range) query.set('mapping', mapping)
  if (range !== OMNI_ROUTE_DEFAULTS.omni_range) query.set('range', range)
  const search = query.toString()
  return '#/omni' + (search ? '?' + search : '')
}

// ---- Perf Eval
// ?ops_perf_view=...&ops_perf_model=...&ops_perf_device=...; ops_detail was written but never restored.

export function perfRoute(params: URLSearchParams): string | null {
  const query = new URLSearchParams(
    perfQuery(
      perfState({ view: params.get('ops_perf_view'), model: params.get('ops_perf_model'), device: params.get('ops_perf_device') }),
    ),
  ).toString()
  return query ? `#/perf?${query}` : '#/perf'
}

const TAB_ROUTES: Record<string, (params: URLSearchParams) => string | null> = {
  projects: () => '#/',
  'ci-health': healthRoute,
  'ci-analytics': analyticsRoute,
  'ci-perf-eval': perfRoute,
  'ci-omni': omniRoute,
  // The Queue tab was retired; its DNS view moved to CI Analytics.
  'ci-queue': queueRoute,
}

/** The hash route for an old link, or null when only the old page serves it. */
export function routeFor(tab: string, params: URLSearchParams): string | null {
  return TAB_ROUTES[tab]?.(params) ?? null
}

/** Rewrites the current location for an old link; returns true if it navigated away. */
export function redirectOldLink(location: Location = window.location): boolean {
  const tab = location.hash.replace(/^#/, '')
  if (!Object.prototype.hasOwnProperty.call(TAB_ROUTES, tab)) return false
  const route = routeFor(tab, new URLSearchParams(location.search))
  if (route) {
    history.replaceState(null, '', location.pathname + route)
    return false
  }
  location.replace(OLD_PAGE + location.search + location.hash)
  return true
}
