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

  it('routes the DNS and agent-health analytics views with their state', () => {
    expect(route('ci-analytics', 'ops_analytics_view=dns')).toBe('#/analytics?view=dns')
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_dns_window=3h&ops_analytics_dns_scope=canonical')).toBe(
      '#/analytics?view=dns&dns_window=3h&dns_scope=canonical',
    )
    // Unknown and default values fall back to the classic defaults, which v2 leaves implicit.
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_dns_window=2h&ops_analytics_dns_scope=amd')).toBe('#/analytics?view=dns')
    expect(route('ci-analytics', 'ops_analytics_view=agent-health')).toBe('#/analytics?view=agent-health')
    expect(
      route(
        'ci-analytics',
        'ops_analytics_view=agent-health&ops_agent_window=30d&ops_agent_gpu=MI355&ops_agent_node=gpu9138&ops_agent_cofail=60&ops_agent_excl_cancel=0&ops_agent_nightly=1&ops_agent_signal=hard',
      ),
    ).toBe(
      '#/analytics?view=agent-health&agent_window=30d&agent_gpu=MI355&agent_node=gpu9138&agent_cofail=60&agent_excl_cancel=0&agent_nightly=1&agent_signal=hard',
    )
    expect(route('ci-analytics', 'ops_analytics_view=agent-health&ops_agent_window=2d&ops_agent_cofail=45&ops_agent_signal=x')).toBe(
      '#/analytics?view=agent-health',
    )
    // A test-group search always opens AMD test health in classic.
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_search=foo')).not.toBe('#/analytics?view=dns')
  })

  it('leaves unported views to the classic page', () => {
    expect(route('ci-health', 'ops_health_view=mirrors')).toBeNull()
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBeNull()
    expect(route('ci-perf-eval')).toBeNull()
    expect(route('ci-omni')).toBeNull()
  })
})
