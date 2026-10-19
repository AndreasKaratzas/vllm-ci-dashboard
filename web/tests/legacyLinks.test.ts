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
    expect(route('ci-health', 'ops_health_view=mirrors')).toBe('#/health?view=mirrors')
    expect(route('ci-health', 'ops_health_view=coverage')).toBe('#/health?view=coverage')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=platform')).toBe('#/health?view=coverage')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=name')).toBe('#/health?view=coverage&sort=name')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=area')).toBe('#/health?view=coverage&sort=area')
    // Classic ignores unknown sort modes.
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=bogus')).toBe('#/health?view=coverage')
  })

  it('leaves unported views to the classic page', () => {
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBeNull()
    expect(route('ci-perf-eval')).toBeNull()
    expect(route('ci-omni')).toBeNull()
  })
})
