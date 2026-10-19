// Classic dashboard links (issue bodies, watchers, bookmarks) use
// ?ops_<state>=...#<tab>. Route ported views to v2 and everything else to the
// classic page, which still serves them.

export const CLASSIC_PAGE = 'classic.html'

const LEGACY_TABS = new Set(['projects', 'ci-health', 'ci-analytics', 'ci-perf-eval', 'ci-omni'])

/** The v2 hash route for a legacy link, or null when only the classic page serves it. */
export function v2RouteFor(tab: string, params: URLSearchParams): string | null {
  if (tab === 'projects') return '#/'
  if (tab === 'ci-health') {
    const view = params.get('ops_health_view')
    // 'targets' was retired into Overview (classic dashboard-nav.js does the same).
    if (!view || view === 'overview' || view === 'targets') return '#/health'
    if (view === 'parity') return '#/health?view=parity'
    return null
  }
  return null
}

/** Rewrites the current location for a legacy link; returns true if it navigated away. */
export function redirectLegacyLink(location: Location = window.location): boolean {
  const tab = location.hash.replace(/^#/, '')
  if (!LEGACY_TABS.has(tab)) return false
  const params = new URLSearchParams(location.search)
  const route = v2RouteFor(tab, params)
  if (route) {
    history.replaceState(null, '', location.pathname + route)
    return false
  }
  location.replace(CLASSIC_PAGE + location.search + location.hash)
  return true
}
