// Classic dashboard links (issue bodies, watchers, bookmarks) use
// ?ops_<state>=...#<tab>. Route ported views to v2 and everything else to the
// classic page, which still serves them. Each area owns its mapping in
// src/legacy/<area>.ts.
import { analyticsRoute, queueRoute } from './legacy/analytics'
import { healthRoute } from './legacy/health'
import { omniRoute } from './legacy/omni'
import { perfRoute } from './legacy/perf'

export const CLASSIC_PAGE = 'classic.html'

const TAB_ROUTES: Record<string, (params: URLSearchParams) => string | null> = {
  projects: () => '#/',
  'ci-health': healthRoute,
  'ci-analytics': analyticsRoute,
  'ci-perf-eval': perfRoute,
  'ci-omni': omniRoute,
  // The Queue tab was retired; classic dashboard-nav.js moves its DNS view to CI Analytics.
  'ci-queue': queueRoute,
}

/** The v2 hash route for a legacy link, or null when only the classic page serves it. */
export function v2RouteFor(tab: string, params: URLSearchParams): string | null {
  return TAB_ROUTES[tab]?.(params) ?? null
}

/** Rewrites the current location for a legacy link; returns true if it navigated away. */
export function redirectLegacyLink(location: Location = window.location): boolean {
  const tab = location.hash.replace(/^#/, '')
  if (!Object.prototype.hasOwnProperty.call(TAB_ROUTES, tab)) return false
  const route = v2RouteFor(tab, new URLSearchParams(location.search))
  if (route) {
    history.replaceState(null, '', location.pathname + route)
    return false
  }
  location.replace(CLASSIC_PAGE + location.search + location.hash)
  return true
}
