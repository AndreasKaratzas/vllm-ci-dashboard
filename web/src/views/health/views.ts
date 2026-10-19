import type { SubNavItem } from '@/components/layout/SubNav.vue'

export type HealthViewId = 'overview' | 'parity' | 'coverage' | 'mirrors'

export interface HealthView extends SubNavItem {
  value: HealthViewId
  description: string
  /** Collector sources the sub-view is built from (keys of config/data_freshness_targets.json). */
  sources: readonly string[]
}

export const HEALTH_VIEWS: HealthView[] = [
  {
    value: 'overview',
    label: 'Overview',
    description: 'Latest main CI AMD gating-job outcomes and failure movement across retained nightlies.',
    sources: ['ci_health'],
  },
  {
    value: 'parity',
    label: 'Upstream parity',
    description: 'Configured AMD route coverage of the current upstream CUDA test groups on main.',
    sources: ['test_group_parity', 'config_parity'],
  },
  {
    value: 'coverage',
    label: 'AMD hardware',
    description: 'Configured AMD test groups by architecture under the fixed best-hardware health policy.',
    sources: ['amd_test_matrix', 'ci_health'],
  },
  {
    value: 'mirrors',
    label: 'AMD mirrors',
    description: 'AMD mirror declarations parsed from every .buildkite/test_areas YAML file on vLLM main.',
    sources: ['test_group_parity'],
  },
]
