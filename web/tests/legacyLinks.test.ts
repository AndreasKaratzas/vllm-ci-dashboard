import { describe, expect, it } from 'vitest'
import { v2RouteFor } from '@/legacyLinks'

const route = (tab: string, query = '') => v2RouteFor(tab, new URLSearchParams(query))

describe('legacy dashboard links', () => {
  it('routes ported views to v2', () => {
    expect(route('projects')).toBe('#/')
    expect(route('ci-health')).toBe('#/health')
    expect(route('ci-health', 'ops_health_view=overview')).toBe('#/health')
    expect(route('ci-health', 'ops_health_view=parity')).toBe('#/health?view=parity')
    // ci_area_regression_watcher links the retired targets view.
    expect(route('ci-health', 'ops_health_view=targets&ops_health_result=non_passing')).toBe('#/health')
  })

  it('routes Omni CI links with their windows', () => {
    expect(route('ci-omni')).toBe('#/omni')
    expect(route('ci-omni', 'ops_omni_mapping_range=1m&ops_omni_range=72h')).toBe('#/omni?mapping=1m&range=72h')
    expect(route('ci-omni', 'ops_omni_mapping_range=7d&ops_omni_range=24h')).toBe('#/omni')
    expect(route('ci-omni', 'ops_omni_range=3h&ops_omni_age=gte3d')).toBe('#/omni?range=3h')
    // classic syncRouteState falls back to the defaults for unknown values.
    expect(route('ci-omni', 'ops_omni_mapping_range=2y&ops_omni_range=9h')).toBe('#/omni')
  })

  it('leaves unported views to the classic page', () => {
    expect(route('ci-health', 'ops_health_view=mirrors')).toBeNull()
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBeNull()
    expect(route('ci-perf-eval')).toBeNull()
  })
})
