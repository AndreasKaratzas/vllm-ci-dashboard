import type { LocationQuery } from 'vue-router'
import type { SubNavItem } from './SubNav.vue'

export interface SubViews {
  /** Query parameter holding the sub-view. */
  param: string
  default: string
  items: SubNavItem[]
}

/** Sub-views of each top-level page, keyed by route name; the breadcrumb and command palette read them. */
export const SUB_VIEWS: Record<string, SubViews> = {
  health: {
    param: 'view',
    default: 'overview',
    items: [
      { value: 'overview', label: 'Overview' },
      { value: 'parity', label: 'Upstream parity' },
      { value: 'coverage', label: 'AMD hardware' },
      { value: 'mirrors', label: 'AMD mirrors' },
    ],
  },
  analytics: {
    param: 'view',
    default: 'groups',
    items: [
      { value: 'groups', label: 'AMD test health' },
      { value: 'latency', label: 'AMD nightly latency' },
      { value: 'nightlies', label: 'AMD nightlies' },
      { value: 'dns', label: 'DNS health' },
      { value: 'agent-health', label: 'CI agent health' },
    ],
  },
  perf: {
    param: 'view',
    default: 'performance',
    items: [
      { value: 'performance', label: 'Performance' },
      { value: 'accuracy', label: 'Accuracy' },
    ],
  },
}

/** The sub-view a route shows, falling back to the page default; null for pages without sub-views. */
export function currentSubView(name: unknown, query: LocationQuery): SubNavItem | null {
  const views = SUB_VIEWS[String(name ?? '')]
  if (!views) return null
  const raw = query[views.param]
  const value = Array.isArray(raw) ? raw[0] : raw
  return views.items.find((item) => item.value === value) ?? views.items.find((item) => item.value === views.default) ?? null
}

/** Route location of one sub-view; the default sub-view keeps the URL bare. */
export function subViewQuery(name: string, value: string): LocationQuery {
  const views = SUB_VIEWS[name]
  return !views || value === views.default ? {} : { [views.param]: value }
}
