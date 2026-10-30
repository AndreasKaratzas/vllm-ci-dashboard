import type { LocationQuery } from 'vue-router'
import { PERF_VIEWS, type PerfView } from '@/domain/perf'
import type { SubNavItem } from './SubNav.vue'

export interface SubViews {
  /** Query parameter holding the sub-view. */
  param: string
  default: string
  items: SubNavItem[]
}

const PERF_VIEW_LABEL: Record<PerfView, string> = { performance: 'Performance', accuracy: 'Accuracy' }

export type SubViewPage = 'main' | 'health' | 'analytics' | 'perf' | 'omni'

/**
 * Sub-views of each top-level page, keyed by route name: the one list the pages' SubNav, their URL
 * state, the breadcrumb, the sidebar and the command palette all read.
 */
export const SUB_VIEWS: Record<SubViewPage, SubViews> = {
  main: {
    param: 'view',
    default: 'overview',
    items: [
      { value: 'overview', label: 'Overview' },
      { value: 'incidents', label: 'Incidents' },
      { value: 'flaky', label: 'Flaky groups' },
    ],
  },
  health: {
    param: 'view',
    default: 'overview',
    items: [
      { value: 'overview', label: 'Overview' },
      { value: 'parity', label: 'Upstream parity' },
      { value: 'coverage', label: 'AMD hardware' },
      { value: 'mirrors', label: 'AMD test groups' },
    ],
  },
  analytics: {
    param: 'view',
    default: 'groups',
    items: [
      { value: 'groups', label: 'AMD test health' },
      { value: 'latency', label: 'AMD nightly latency' },
      { value: 'slowdowns', label: 'Slowdowns' },
      { value: 'nightlies', label: 'AMD nightlies' },
      { value: 'dns', label: 'DNS health' },
      { value: 'agent-health', label: 'CI agent health' },
      { value: 'docker', label: 'Docker builds' },
    ],
  },
  perf: {
    param: 'view',
    default: 'performance',
    items: PERF_VIEWS.map((value) => ({ value, label: PERF_VIEW_LABEL[value] })),
  },
  omni: {
    param: 'view',
    default: 'workload',
    items: [
      { value: 'workload', label: 'Incoming workload' },
      { value: 'live', label: 'Live queues' },
    ],
  },
}

/** The sub-views of a route, if it has any. */
export const pageViews = (name: unknown): SubViews | undefined => (SUB_VIEWS as Record<string, SubViews | undefined>)[String(name ?? '')]

/** The sub-view ids of one page, for URL state validation. */
export const subViewIds = <T extends string = string>(page: SubViewPage): T[] => SUB_VIEWS[page].items.map((item) => item.value as T)

/** The sub-view a route shows, falling back to the page default; null for pages without sub-views. */
export function currentSubView(name: unknown, query: LocationQuery): SubNavItem | null {
  const views = pageViews(name)
  if (!views) return null
  const raw = query[views.param]
  const value = Array.isArray(raw) ? raw[0] : raw
  return views.items.find((item) => item.value === value) ?? views.items.find((item) => item.value === views.default) ?? null
}

/** Route location of one sub-view; the default sub-view keeps the URL bare. */
export function subViewQuery(name: string, value: string): LocationQuery {
  const views = pageViews(name)
  return !views || value === views.default ? {} : { [views.param]: value }
}
