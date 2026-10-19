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

  it('leaves unported views to the classic page', () => {
    expect(route('ci-health', 'ops_health_view=mirrors')).toBeNull()
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBeNull()
    expect(route('ci-perf-eval')).toBeNull()
    expect(route('ci-omni')).toBeNull()
  })
})
