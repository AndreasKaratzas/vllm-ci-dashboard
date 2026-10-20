import { SUB_VIEWS } from '@/components/layout/navigation'
import type { SubNavItem } from '@/components/layout/SubNav.vue'

export type HealthViewId = 'overview' | 'parity' | 'coverage' | 'mirrors'

interface HealthViewInfo {
  description: string
  /** Collector sources the sub-view is built from (keys of config/data_freshness_targets.json). */
  sources: readonly string[]
}

export type HealthView = SubNavItem & HealthViewInfo & { value: HealthViewId }

const INFO: Record<HealthViewId, HealthViewInfo> = {
  overview: {
    description: 'Latest main CI AMD gating-job outcomes and failure movement across retained nightlies.',
    sources: ['ci_health', 'test_group_parity'],
  },
  parity: {
    description: 'Configured AMD route coverage of the current upstream CUDA test groups on main.',
    sources: ['test_group_parity', 'config_parity'],
  },
  coverage: {
    description: 'Configured AMD test groups by architecture under the fixed best-hardware health policy.',
    sources: ['amd_test_matrix', 'ci_health'],
  },
  mirrors: {
    description: 'AMD mirror declarations parsed from every .buildkite/test_areas YAML file on vLLM main.',
    sources: ['test_group_parity'],
  },
}

export const HEALTH_VIEWS: HealthView[] = SUB_VIEWS.health.items.map((item) => ({ ...item, ...INFO[item.value as HealthViewId], value: item.value as HealthViewId }))
